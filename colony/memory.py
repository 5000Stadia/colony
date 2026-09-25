"""The spine, NOW and the ledger, and the brief assembled from them for each step."""
import re
import secrets
import time

from . import field, mapper

ROW = re.compile(r"^\|\s*(\d+)\s*\|(.*?)\|(.*?)\|(?:\s*(\d+)\s*(?:/\s*10\s*)?(?:[—–-]+\s*([^|]*?))?\s*\|)?\s*$")


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


def proposed(project):
    """Rows builders proposed under '## Proposed rows'; they join the plan only when the person moves them."""
    text = project.spine.read_text() if project.spine.exists() else ""
    m = re.search(r"^##\s+Proposed rows\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    return [(int(r.group(1)), r.group(2).strip(), r.group(3).strip())
            for r in (ROW.match(l.strip()) for l in m.group(1).splitlines()) if r] if m else []


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
    lines, section, kept = project.spine.read_text().split("\n"), None, []
    for l in lines:
        if l.startswith("## "):
            section = l[3:].strip().lower()
        if section == "the spec list" and (m := ROW.match(l.strip())) and int(m.group(1)) == number:
            continue
        kept.append(l)
    project.spine.write_text("\n".join(kept))


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


def specialist_brief(project, row, doubt=""):
    """A reviewer's brief: where to look first (what the assigner said is at stake, what the builder is
    least sure of), the goal, what must never happen, and the row — nothing it does not need."""
    number, target, done = row
    impact_n, stake = stakes(project, number)
    look = []
    if impact_n is not None:
        look.append(f"- What is at stake (set when the row was assigned): impact {impact_n}/10" + (f" — {stake}" if stake else ""))
    if doubt:
        look.append(f"- What the builder is least sure of: {doubt}")
    return "\n\n".join((["# Look here first\n\n" + "\n".join(look)] if look else []) + [
        "# The goal\n\n" + goal(project),
        "# What must never happen\n\n" + (irreversible(project) or "nothing listed"),
        f"# This row\n\nRow {number}: {target}\nDone looks like: {done}",
    ])


def brief(project, row):
    """What the builder needs for this row, and nothing else: the project itself is the state."""
    number, target, done = row
    open_ = [field.render(s) for s in field.signals(project, row=number)]
    return "\n\n".join([
        "# The goal (the person's words — never change them)\n\n" + project.spine.read_text(),
        f"# This row\n\nRow {number}: {target}\nDone looks like: {done}"
        + ("".join(f"\n\nA note from the person on this row ({n['at'][:10]}): {n['text']}" for n in waiting_notes(project, number))),
        "# What already exists\n\nAsk the map before making anything new: `python3 -m colony map QUERY`.",
        "# Open signals on this row\n\n" + ("\n".join(open_) if open_ else "none"),
    ])
