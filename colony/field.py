"""The field: short, located signals that specialists leave and the builder answers.

A signal at the same place as an open one reinforces it: its strength is the number of different
lineages that found it.
"""
import fcntl
import json

RANK = {"critical": 3, "major": 2, "minor": 1}
KINDS = ("hole", "gap", "friction", "duplicate", "fork", "check", "unfixed")


def signals(project, row=None, wave=0):
    """Fold the field's log into the open signals, strongest first."""
    open_, by_place = {}, {}
    for e in project.read("field.jsonl"):
        if row is not None and e.get("row") != row:
            continue
        if e["type"] == "signal":
            place = e["at"].strip()
            if place in by_place and by_place[place] in open_:
                s = open_[by_place[place]]
                s["by"].add(e["by"].split("@")[0])
                s["last_wave"] = max(s["last_wave"], e["wave"])
                if RANK[e["severity"]] > RANK[s["severity"]]:
                    s["severity"] = e["severity"]
                s["notes"].append(e["text"])
            else:
                open_[e["id"]] = {"id": e["id"], "row": e.get("row"), "kind": e["kind"], "severity": e["severity"],
                                  "at": place, "by": {e["by"].split("@")[0]}, "wave": e["wave"], "last_wave": e["wave"],
                                  "notes": [e["text"]]}
                by_place[place] = e["id"]
        elif e["type"] == "resolve":
            open_.pop(e["of"], None)
    live = list(open_.values())
    for s in live:
        s["strength"] = len(s["by"])
    return sorted(live, key=lambda s: (-s["strength"], -RANK[s["severity"]], s["id"]))


def wakes_builder(s, wave):
    """Critical, a failing check, a fork or a fix that did not hold always; major only in the first
    wave; later, confirmation."""
    if s["severity"] == "critical" or s["kind"] in ("check", "fork", "unfixed"):
        return True
    if wave <= 1 and s["severity"] == "major":
        return True
    return s["strength"] >= 2 and s["severity"] != "minor"


def _append_numbered(project, record):
    """Number and append one event under a lock: specialists post in parallel, and two of them must
    never be given the same id, or one signal's confirmation and lesson would be lost."""
    project.state.mkdir(parents=True, exist_ok=True)
    with open(project.state / "field.jsonl", "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        n = sum(1 for line in fh if line.strip()) + 1
        fh.write(json.dumps(dict(record, id=n), ensure_ascii=False) + "\n")
        fh.flush()
    return n


def post(project, *, by, row, wave, kind, severity, at, text):
    if kind not in KINDS or severity not in RANK:
        raise ValueError(f"kind must be one of {KINDS}; severity one of {tuple(RANK)}")
    return _append_numbered(project, {"type": "signal", "by": by, "row": row, "wave": wave, "kind": kind,
                                      "severity": severity, "at": at, "text": text})


def resolve(project, *, by, row, wave, of, text, fixed):
    if of not in {s["id"] for s in signals(project, row=row, wave=wave)}:
        raise ValueError(f"#{of} is not an open signal on row {row}")
    return _append_numbered(project, {"type": "resolve", "by": by, "row": row, "wave": wave, "of": of,
                                      "fixed": fixed, "text": text})


def render(sig):
    return (f"#{sig['id']} [{sig['severity']} {sig['kind']}, strength {sig['strength']}] {sig['at']}: "
            + " | ".join(sig["notes"]))
