"""Specialists are lineages: a mission and a bounded memory of what has actually paid off.

A lineage's memory gains a line only for a signal of its that the builder fixed — never for a guess,
a decline or a signal nobody acted on — so the next wave starts from what has proven real.
"""
import re

MEMORY_LINES = 12
ALWAYS = {
    "reuse": "Compare this change against the map of what already exists. Signal (kind `duplicate`) "
             "anything it re-makes that the work already has — a function, a section, a character "
             "thread, an offer — and anything it should have built on and did not.",
    "fresh-eyes": "Meet the work the way its real audience would, from its front door, with no account of "
                  "how it was made. Signal where that person would stumble, be misled or give up.",
}


def load(project):
    out = {}
    for path in sorted(project.specialists.glob("*.md")):
        text = path.read_text()
        mission = re.search(r"^##\s+Mission\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
        memory = re.search(r"^##\s+Memory\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
        out[path.stem] = {"mission": mission.group(1).strip() if mission else "",
                          "memory": [l[2:] for l in (memory.group(1) if memory else "").splitlines() if l.startswith("- ")]}
    return out


def write(project, name, mission, memory=()):
    project.specialists.mkdir(parents=True, exist_ok=True)
    body = f"# {name}\n\n## Mission\n\n{mission.strip()}\n\n## Memory\n\n" + "".join(f"- {m}\n" for m in memory)
    (project.specialists / f"{name}.md").write_text(body)


def ensure_defaults(project):
    have = load(project)
    for name, mission in ALWAYS.items():
        if name not in have:
            write(project, name, mission)


def harvest(project, row):
    """Give each lineage whose signal was fixed on this row one line of memory."""
    events = project.read("field.jsonl")
    signals = {e["id"]: e for e in events if e["type"] == "signal" and e.get("row") == row}
    fixed = [e["of"] for e in events if e["type"] == "resolve" and e.get("row") == row and e.get("fixed")]
    lineages = load(project)
    gained = {}
    for sid in fixed:
        sig = signals.get(sid)
        if not sig:
            continue
        place = sig["at"]
        finders = {e["by"] for e in signals.values() if e["at"].strip() == place.strip()}
        for by in finders:
            name = by.split("@")[0]
            if name in lineages:
                lesson = f"row {row}: {sig['kind']} at {place} — {sig['text'][:160]}"
                gained.setdefault(name, []).append(lesson)
    for name, lessons in gained.items():
        memory = (lineages[name]["memory"] + lessons)[-MEMORY_LINES:]
        write(project, name, lineages[name]["mission"], memory)
    return gained
