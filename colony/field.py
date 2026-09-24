"""The field: short, located signals that specialists leave and the builder answers.

A signal at the same place as an open one reinforces it: its strength is the number of different
agents that found it. A minor signal nobody else confirms fades after the next wave.
"""
RANK = {"critical": 3, "major": 2, "minor": 1}
KINDS = ("hole", "gap", "friction", "duplicate", "fork", "check")


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
                s["by"].add(e["by"])
                s["last_wave"] = max(s["last_wave"], e["wave"])
                if RANK[e["severity"]] > RANK[s["severity"]]:
                    s["severity"] = e["severity"]
                s["notes"].append(e["text"])
            else:
                open_[e["id"]] = {"id": e["id"], "row": e.get("row"), "kind": e["kind"], "severity": e["severity"],
                                  "at": place, "by": {e["by"]}, "wave": e["wave"], "last_wave": e["wave"],
                                  "notes": [e["text"]]}
                by_place[place] = e["id"]
        elif e["type"] == "resolve":
            open_.pop(e["of"], None)
    live = []
    for s in open_.values():
        s["strength"] = len(s["by"])
        faded = s["severity"] == "minor" and s["strength"] == 1 and wave - s["last_wave"] >= 2
        if not faded:
            live.append(s)
    return sorted(live, key=lambda s: (-s["strength"], -RANK[s["severity"]], s["id"]))


def wakes_builder(s, wave):
    """Critical, a failing check, or a fork always; major only in the first wave; later, confirmation."""
    if s["severity"] == "critical" or s["kind"] in ("check", "fork"):
        return True
    if wave <= 1 and s["severity"] == "major":
        return True
    return s["strength"] >= 2 and s["severity"] != "minor"


def post(project, *, by, row, wave, kind, severity, at, text):
    if kind not in KINDS or severity not in RANK:
        raise ValueError(f"kind must be one of {KINDS}; severity one of {tuple(RANK)}")
    n = len(project.read("field.jsonl")) + 1
    project.append("field.jsonl", {"type": "signal", "id": n, "by": by, "row": row, "wave": wave, "kind": kind,
                                   "severity": severity, "at": at, "text": text})
    return n


def resolve(project, *, by, row, wave, of, text, fixed):
    if of not in {s["id"] for s in signals(project, row=row, wave=wave)}:
        raise ValueError(f"#{of} is not an open signal on row {row}")
    n = len(project.read("field.jsonl")) + 1
    project.append("field.jsonl", {"type": "resolve", "id": n, "by": by, "row": row, "wave": wave, "of": of,
                                   "fixed": fixed, "text": text})
    return n


def render(sig):
    return (f"#{sig['id']} [{sig['severity']} {sig['kind']}, strength {sig['strength']}] {sig['at']}: "
            + " | ".join(sig["notes"]))
