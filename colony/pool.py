"""The person's subscriptions as one pool (R169): a project's helper runs on whichever program does its tier best for
what it costs of that program's week, and the week's pace.

Both subscriptions are paid either way, so what a task costs is the share of its program's week it takes, not its
dollars: a pair's price per task (Artificial Analysis, at API prices) over what a whole week of that program holds at
the same prices. What a week holds is measured here, from every session's tokens since the week began (tokens.py)
against the percent of it used: on Nov 17 Claude's held about $3,000 of work and Codex's about $680, so Codex, far
cheaper per task in dollars, was not the cheaper by its week. A week running ahead of its pace weighs dearer, one
behind cheaper, so both are used nearly whole by their resets, and neither runs out early.

Only work that starts afresh anyway crosses: a helper rebuilds its context whichever program runs it (measured: about
40% of a build helper's run goes before its first edit, on either), so its program costs no extra handoff. A
project's agent never moves: its conversation would be read again in full, without the cache. The small tiers stay
home too: their tasks are small against the cost of starting a run elsewhere.
"""
import json
import time

from . import board, providers

CROSS = ("routine", "step-up")      # the tiers whose tasks are big enough to run on another program
RESERVE = 90                        # percent of a week past which no helper goes to that program: consultations keep it
FLOOR = 10                          # percent of a week used before what it holds is estimated afresh
WEEK = 7 * 24 * 3600
SMOOTH = 5                          # points added to used and pace both, so the first hours of a week don't swing it


def _path():
    return board.home() / "usage" / "holds.json"


def _load():
    try:
        return json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}


def week(key):
    """A program's week now: (percent used, when it resets), or (None, None) without a reading."""
    from . import usage
    w = usage.read(key).get("weekly") or {}
    return w.get("used"), w.get("resets_at")


def holds(key, now=None):
    """What a program's whole week holds, in dollars at API prices: what it has spent since its week began over the
    percent used. Kept as last estimated while too little of the week is used to tell; None before any estimate."""
    from . import tokens
    now = now or time.time()
    used, resets = week(key)
    saved = _load()
    if used and used >= FLOOR and resets and resets > now:
        spent = tokens.dollars(key, resets - WEEK, now)
        if spent > 0:
            saved[key] = {"dollars": round(spent * 100 / used, 2), "at": now, "used": used}
            _path().parent.mkdir(parents=True, exist_ok=True)
            _path().write_text(json.dumps(saved))
    return (saved.get(key) or {}).get("dollars")


def pressure(key, now=None):
    """How far a program's week runs ahead of an even pace to its reset: above 1 ahead (its week weighs dearer), below
    1 behind. None without a reading."""
    now = now or time.time()
    used, resets = week(key)
    if used is None or not resets or resets <= now:
        return None
    pace = 100 * min(1, max(0, (now - (resets - WEEK)) / WEEK))
    return (used + SMOOTH) / (pace + SMOOTH)


def room(root, key):
    """Whether a project's helper may go to a program: it is here and usable, and its week is below both the
    project's limit for it (R168) and the reserve kept for consultations."""
    from . import usage
    p = providers.PROVIDERS.get(key)
    if not p or not providers.usable(p):
        return False
    used, _ = week(key)
    return used is not None and used < min(RESERVE, usage.week_limits(root).get(key, 100))


def _measured(value):
    ev = (value or {}).get("evidence") or {}
    return ev.get("score"), ev.get("cost")


def choose(root, tier, now=None):
    """The program and pair a project's helper tier runs on this time: its own program's pick, unless another program's
    pick for the tier is at least as smart (within Auto's tolerance) and takes less of its week, pace weighed in.
    {program, model, effort, why}. A tier the project chose its own model for stays its own."""
    from . import intelligence, selection
    home = providers.key(providers.of(root))
    mine = selection.helper(root, tier)
    out = dict(program=home, model=mine["model"], effort=mine.get("effort"), why="its own program")
    if tier not in CROSS or mine.get("own") or (selection.pin_of(root, tier) or {}).get("model"):
        return out
    score, cost = _measured(mine)
    base = (holds(home, now), pressure(home, now))
    if None in (score, cost, *base) or not base[0]:
        return out
    price, best = cost / base[0] * base[1], None
    for key in providers.PROVIDERS:
        if key == home or not room(root, key):
            continue
        try:
            theirs = selection.auto(key, tier)
        except selection.Unavailable:
            continue
        s, c = _measured(theirs)
        h, pr = holds(key, now), pressure(key, now)
        if None in (s, c, h, pr) or not h or s < score - intelligence.TOLERANCE:
            continue
        p = c / h * pr
        if p < price and (best is None or p < best[0]):
            label = providers.get(key).label
            best = (p, dict(program=key, model=theirs["model"], effort=theirs.get("effort"),
                            why=f"{label}'s pick for the tier scores {s:.1f} to {score:.1f} and takes "
                                f"{100 * p / price:.0f}% as much of its week, pace weighed in"))
    return best[1] if best else out
