"""The spine, NOW and the ledger, and the brief assembled from them for each step."""
import re
import secrets
import time

from . import field, mapper

ROW = re.compile(r"^\|\s*(\d+)\s*\|(.*?)\|(.*?)\|(?:\s*(\d+)\s*(?:[—–-]+\s*([^|]*?))?\s*\|)?\s*$")


NOTES = "notes.jsonl"


def notes(project):
    """Notes the person left on rows from the page, oldest first, with whether each was folded in."""
    merged = {}
    for e in project.read(NOTES):
        merged.setdefault(e["id"], {}).update(e)
    return sorted(merged.values(), key=lambda n: n.get("at", ""))


def add_note(project, row, author, text):
    entry = {"id": secrets.token_hex(5), "row": row, "author": author or "person", "text": text.strip(),
             "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "folded": False}
    project.append(NOTES, entry)
    return entry


def waiting_notes(project, row):
    return [n for n in notes(project) if n.get("row") == row and not n.get("folded")]


def fold_notes(project, row):
    """A row's notes were in its builder's brief; once the row closes they are marked as folded in."""
    for n in waiting_notes(project, row):
        project.append(NOTES, {"id": n["id"], "folded": True, "folded_row": row})


def rows(project):
    """The open rows of the spine's spec list, in order: (number, target, done)."""
    text = project.spine.read_text()
    m = re.search(r"^##\s+The spec list\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    if not m:
        return []
    return [(int(r.group(1)), r.group(2).strip(), r.group(3).strip())
            for r in (ROW.match(line.strip()) for line in m.group(1).splitlines()) if r]


def stakes(project, number):
    """What the row's assigner said is at stake — (impact 1 to 10, one sentence) — or (None, "")."""
    for line in project.spine.read_text().splitlines():
        r = ROW.match(line.strip())
        if r and int(r.group(1)) == number and r.group(4):
            return min(max(int(r.group(4)), 1), 10), (r.group(5) or "").strip()
    return None, ""


def impact(project, number):
    return stakes(project, number)[0]


def approved(project):
    return bool(re.search(r"^\*\*Approved:\*\*\s*yes\b", project.spine.read_text(), re.M | re.I))


def checks(project):
    """Commands the spine names under '## Checks', each in backticks on its own bullet."""
    text = project.spine.read_text()
    m = re.search(r"^##\s+Checks\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    return re.findall(r"^\s*-\s*`([^`]+)`", m.group(1), re.M) if m else []


def rules(project):
    """The person's standing answers, under the spine's '## Rules': `- `kind` — what to do`."""
    text = project.spine.read_text() if project.spine.exists() else ""
    m = re.search(r"^##\s+Rules\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    return dict(re.findall(r"^\s*-\s*`([\w-]+)`\s*[—–-]+\s*(.+?)\s*$", m.group(1), re.M)) if m else {}


def add_rule(project, kind, text):
    """Keep an answer as a rule in the spine, where the person reads and edits it."""
    body = project.spine.read_text().rstrip("\n")
    line = f"- `{kind}` — {text.strip()}"
    if re.search(r"^##\s+Rules\s*$", body, re.M):
        body = re.sub(r"(^##\s+Rules\s*$)", lambda m: m.group(1) + "\n" + line, body, count=1, flags=re.M)
    else:
        body += "\n\n## Rules\n" + line
    project.spine.write_text(body + "\n")


def answer(project, kind, text, always=False):
    """The person's answer to a checkpoint question reaches the next row's builder as their note; kept
    as a rule, it settles the same question at later checkpoints."""
    add_note(project, next_row(project), "person", f"On `{kind}`: {text}")
    ledger(project, "answer", question=kind, text=text, always=always)
    if always:
        add_rule(project, kind, text)


def open_questions(project):
    """The last checkpoint's questions the person has not answered yet."""
    entries = project.read("ledger.jsonl")
    marks = [i for i, e in enumerate(entries) if e["kind"] == "checkpoint"]
    if not marks:
        return []
    answered = {e["question"] for e in entries[marks[-1]:] if e["kind"] == "answer"}
    return [q for q in entries[marks[-1]].get("questions", []) if q["kind"] not in answered]


def next_row(project):
    open_rows = rows(project) if project.spine.exists() else []
    return open_rows[0][0] if open_rows else 0


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


def risky(project):
    """Path prefixes listed under '## Risky areas' in the spine, each in backticks."""
    text = project.spine.read_text()
    m = re.search(r"^##\s+Risky areas\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    return re.findall(r"`([^`]+)`", m.group(1)) if m else []


def goal(project):
    text = project.spine.read_text()
    m = re.search(r"^##\s+What we're making\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def specialist_brief(project, row, changed, assessment=None):
    """A specialist's brief: where to look first (what the assigner said is at stake, what the builder is
    least sure of), the goal, what must never happen, the row, and the map entries for the files that
    changed — not the whole spine or NOW, which it does not need to attack a change."""
    number, target, done = row
    bearing = mapper.query(project, " ".join(changed) + f" {target}")
    impact_n, stake = stakes(project, number)
    look = []
    if impact_n is not None:
        look.append(f"- What is at stake (set when the row was assigned): impact {impact_n}/10" + (f" — {stake}" if stake else ""))
    if assessment and assessment.get("confidence") is not None:
        look.append(f"- Where the builder is least sure: confidence {assessment['confidence']}/10"
                    + (f" — {assessment['note']}" if assessment.get("note") else ""))
    return "\n\n".join((["# Look here first\n\n" + "\n".join(look)] if look else []) + [
        "# The goal\n\n" + goal(project),
        "# What must never happen\n\n" + (irreversible(project) or "nothing listed"),
        f"# This row\n\nRow {number}: {target}\nDone looks like: {done}",
        "# What already exists around the change (from the map)\n\n" + mapper.render(bearing),
    ])


def brief(project, row, extra=""):
    """What the builder needs for this row, and nothing else."""
    number, target, done = row
    open_ = [field.render(s) for s in field.signals(project, row=number)]
    bearing = ("# What already exists that bears on it (from the map; read these lines, not everything)\n\n"
               + mapper.render(mapper.query(project, f"{target} {done} {extra}"))
               if project.config().get("map_in_brief") else
               "# What already exists\n\nAsk the map before making anything new: `python3 -m colony map QUERY`.")
    return "\n\n".join([
        "# The goal (the person's words — never change them)\n\n" + project.spine.read_text(),
        "# Where the project is now\n\n" + now_text(project)
        + ("\n\n`git log` holds what every earlier row did and why, including decisions that were later "
           "changed; read the entries that bear on this row." if project.config().get("reconcile") else ""),
        f"# This row\n\nRow {number}: {target}\nDone looks like: {done}"
        + ("".join(f"\n\nA note from the person on this row ({n['at'][:10]}): {n['text']}" for n in waiting_notes(project, number))),
        bearing,
        "# Open signals on this row\n\n" + ("\n".join(open_) if open_ else "none"),
    ])
