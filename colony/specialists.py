"""Specialists are lineages: a mission and a bounded memory of what has actually paid off.

A lineage's memory gains a line only for a serious signal of its that the builder fixed — critical,
or found by two lineages — never for a guess, a decline or trivia, so the next wave starts from what
has proven real.
"""
import re

from . import field

MEMORY_LINES = 12
ALWAYS = {
    "critic": "You did not build this and know nothing of how its builder read the goal. Find where it "
              "fails the goal — every failure a user would hit, every silent failure nobody would notice, "
              "and what the goal implies that is missing. Try it the wrong way. Demonstrate each problem.",
}


def load(project):
    """The colony's reviewers: Claude Code subagent files marked `colony: reviewer`."""
    out = {}
    for path in sorted(project.specialists.glob("*.md")):
        text = path.read_text()
        m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
        if not m or not re.search(r"^colony:\s*reviewer\s*$", m.group(1), re.M):
            continue                      # someone's own subagent, not a colony reviewer
        body = m.group(2)
        mission, _, memory = body.partition("## Memory")
        out[path.stem] = {"mission": mission.strip(),
                          "memory": [l[2:] for l in memory.splitlines() if l.startswith("- ")]}
    return out


def write(project, name, mission, memory=()):
    project.specialists.mkdir(parents=True, exist_ok=True)
    first = re.split(r"(?<=[.!?])\s", mission.strip())[0].replace('"', "'")
    front = (f"---\nname: {name}\ndescription: \"{first} A colony reviewer: reads, never edits.\"\n"
             "tools: Read, Grep, Glob, Bash\ncolony: reviewer\n---\n")
    body = f"{mission.strip()}\n\n## Memory\n\n" + "".join(f"- {m}\n" for m in memory)
    (project.specialists / f"{name}.md").write_text(front + body)


def ensure_defaults(project):
    have = load(project)
    for name, mission in ALWAYS.items():
        if name not in have:
            write(project, name, mission)


def harvest(project, row):
    """Give each lineage whose serious signal was fixed on this row one line of memory: lessons from
    trivia would teach reviewers to find more trivia."""
    events = project.read("field.jsonl")
    signals = {e["id"]: e for e in events if e["type"] == "signal" and e.get("row") == row}
    _, serious = field.review_fixes(events, row)
    lineages = load(project)
    gained = {}
    for sig in serious:
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
