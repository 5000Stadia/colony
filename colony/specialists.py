"""Reviewers are Claude Code subagent files marked `colony: reviewer`: a mission, read-only, and the
lessons of what they found before. A lesson is kept because work like it comes again, and whoever
does not know it is likely to repeat the miss; it is kept only from a serious fix (critical, or found
by two reviewers), so trivia never teaches reviewers to look for more trivia.
"""
import re

from . import field

LESSONS = 12
ALWAYS = {
    "critic": "You did not build this and know nothing of how its builder read the goal. Find where it "
              "fails the goal — every failure a user would hit, every silent failure nobody would notice, "
              "and what the goal implies that is missing. Try it the wrong way. Demonstrate each problem.",
}


def load(project):
    """The project's reviewers: {name: {"mission", "lessons"}}; someone's own subagents are left alone."""
    out = {}
    for path in sorted(project.specialists.glob("*.md")):
        m = re.match(r"^---\n(.*?)\n---\n(.*)$", path.read_text(), re.S)
        if m and re.search(r"^colony:\s*reviewer\s*$", m.group(1), re.M):
            mission, _, lessons = m.group(2).partition("## Lessons")
            out[path.stem] = {"mission": mission.partition("## Memory")[0].strip(),
                              "lessons": [l[2:] for l in lessons.splitlines() if l.startswith("- ")]}
    return out


def write(project, name, mission, lessons=()):
    project.specialists.mkdir(parents=True, exist_ok=True)
    first = re.split(r"(?<=[.!?])\s", mission.strip())[0].replace('"', "'")
    front = (f"---\nname: {name}\ndescription: \"{first} A colony reviewer: reads, never edits.\"\n"
             "tools: Read, Grep, Glob, Bash\ncolony: reviewer\n---\n")
    body = mission.strip() + "\n" + ("\n## Lessons\n\n" + "".join(f"- {l}\n" for l in lessons) if lessons else "")
    (project.specialists / f"{name}.md").write_text(front + body)


def ensure_defaults(project):
    have = load(project)
    for name, mission in ALWAYS.items():
        if name not in have:
            write(project, name, mission)


def harvest(project, row):
    """Each reviewer whose serious finding was fixed on this row keeps one line of it."""
    events = project.read("field.jsonl")
    _, serious = field.review_fixes(events, row)
    reviewers = load(project)
    gained = {}
    for sig in serious:
        finders = {e["by"].split("@")[0] for e in events if e["type"] == "signal" and e.get("row") == row
                   and e["at"].strip() == sig["at"].strip()}
        for name in finders & set(reviewers):
            gained.setdefault(name, []).append(f"row {row}: {sig['kind']} at {sig['at']} — {sig['text'][:160]}")
    for name, lessons in gained.items():
        write(project, name, reviewers[name]["mission"], (reviewers[name]["lessons"] + lessons)[-LESSONS:])
    return gained
