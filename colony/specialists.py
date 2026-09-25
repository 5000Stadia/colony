"""Reviewers are Claude Code subagent files marked `colony: reviewer`: one mission each, read-only."""
import re

ALWAYS = {
    "critic": "You did not build this and know nothing of how its builder read the goal. Find where it "
              "fails the goal — every failure a user would hit, every silent failure nobody would notice, "
              "and what the goal implies that is missing. Try it the wrong way. Demonstrate each problem.",
}


def load(project):
    """The project's reviewers and their missions; someone's own subagents are left alone."""
    out = {}
    for path in sorted(project.specialists.glob("*.md")):
        m = re.match(r"^---\n(.*?)\n---\n(.*)$", path.read_text(), re.S)
        if m and re.search(r"^colony:\s*reviewer\s*$", m.group(1), re.M):
            out[path.stem] = m.group(2).partition("## Memory")[0].strip()
    return out


def write(project, name, mission):
    project.specialists.mkdir(parents=True, exist_ok=True)
    first = re.split(r"(?<=[.!?])\s", mission.strip())[0].replace('"', "'")
    front = (f"---\nname: {name}\ndescription: \"{first} A colony reviewer: reads, never edits.\"\n"
             "tools: Read, Grep, Glob, Bash\ncolony: reviewer\n---\n")
    (project.specialists / f"{name}.md").write_text(front + mission.strip() + "\n")


def ensure_defaults(project):
    have = load(project)
    for name, mission in ALWAYS.items():
        if name not in have:
            write(project, name, mission)
