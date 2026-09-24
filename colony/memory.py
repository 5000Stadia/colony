"""The spine, NOW and the ledger, and the brief assembled from them for each step."""
import re
import time

from . import field, mapper

ROW = re.compile(r"^\|\s*(\d+)\s*\|(.*?)\|(.*?)\|\s*$")


def rows(project):
    """The open rows of the spine's spec list, in order: (number, target, done)."""
    text = project.spine.read_text()
    m = re.search(r"^##\s+The spec list\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    if not m:
        return []
    return [(int(r.group(1)), r.group(2).strip(), r.group(3).strip())
            for r in (ROW.match(line.strip()) for line in m.group(1).splitlines()) if r]


def approved(project):
    return bool(re.search(r"^\*\*Approved:\*\*\s*yes\b", project.spine.read_text(), re.M | re.I))


def checks(project):
    """Commands the spine names under '## Checks', each in backticks on its own bullet."""
    text = project.spine.read_text()
    m = re.search(r"^##\s+Checks\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    return re.findall(r"^\s*-\s*`([^`]+)`", m.group(1), re.M) if m else []


def irreversible(project):
    text = project.spine.read_text()
    m = re.search(r"^##\s+What it must never do\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def close_row(project, number):
    """Remove the row from the spine; git remembers it."""
    lines = project.spine.read_text().split("\n")
    kept = [l for l in lines if not ((m := ROW.match(l.strip())) and int(m.group(1)) == number)]
    project.spine.write_text("\n".join(kept))


def now_text(project):
    return project.now.read_text() if project.now.exists() else "Nothing has been built yet."


def ledger(project, kind, **fields):
    project.append("ledger.jsonl", dict(fields, kind=kind, at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))


def brief(project, row, extra=""):
    """What an agent needs for this row, and nothing else."""
    number, target, done = row
    bearing = mapper.query(project, f"{target} {done} {extra}")
    open_ = [field.render(s) for s in field.signals(project, row=number)]
    return "\n\n".join([
        "# The goal (the person's words — never change them)\n\n" + project.spine.read_text(),
        "# Where the project is now\n\n" + now_text(project)
        + ("\n\n`design/history.md` records what every earlier row did and why, including decisions that were "
           "later changed; read the entries that bear on this row." if (project.design / "history.md").exists() else ""),
        f"# This row\n\nRow {number}: {target}\nDone looks like: {done}",
        "# What already exists that bears on it (from the map; read these lines, not everything)\n\n"
        + mapper.render(bearing),
        "# Open signals on this row\n\n" + ("\n".join(open_) if open_ else "none"),
    ])
