"""Supports: tools a project's agent can be offered when its work calls for one, as a coach, physio or
water carrier serves a player. The monitor keeps this list; project agents never read it, so it adds
nothing to their instructions.

A support is offered for a need the work has shown, never installed by default, and trusted in steps:
  candidate  found for a need with evidence; conceptually likely to help, not yet shown
  used       helped in real use, without a controlled comparison
  testing    the person said to test it
  proven     beat the plain setup on the project's own kind of work, past run-to-run noise
  rejected   failed its test, or declined; kept with the reason so it isn't proposed again
Admission: free, runs locally, no account or login, removes cleanly.

A reference is the other kind: a project elsewhere that does something this one does, better. It installs
nothing and costs a read, so the agent that reads it is its test; it may be suggested once the monitor
has read it and can say where the better way is.

PROVIDER: the seeds are Claude Code plugins; a project run by another provider needs that CLI's own
equivalent, and the monitor looks for it the same way.
"""
import json

from . import board

STATUSES = ("candidate", "used", "testing", "proven", "rejected")

# Where the list starts: what our own work has already surfaced.
SEED = [
    {"name": "language server (pyright-lsp, typescript-lsp)", "source": "Claude Code plugin marketplace (claude-plugins-official)",
     "symptom": "a large codebase where the agent searches and opens many files to find a definition or its callers",
     "gives": "exact answers to where is this defined, who calls it, what uses it",
     "cost": "a local language server process", "remove": "uninstall the plugin", "status": "candidate",
     "evidence": "Benzi's own run log (vendor-run, 25 bugs, Sonnet): same solve rate at about half the cost with a code map; not yet tested by us"},
    {"name": "playwright", "source": "Claude Code plugin marketplace (Microsoft's MCP server)",
     "symptom": "UI or phone-layout work checked only by reading code",
     "gives": "drives a real browser: screenshots at phone and desktop sizes, clicks, what the page actually shows",
     "cost": "a local browser", "remove": "uninstall the plugin", "status": "used",
     "evidence": "Bookflow's mobile and desktop audits and the board's phone work ran on it"},
    {"name": "chrome-devtools-mcp", "source": "Claude Code plugin marketplace",
     "symptom": "a web page that is slow or misbehaves only when running",
     "gives": "performance traces, console and network from a live browser",
     "cost": "a local browser", "remove": "uninstall the plugin", "status": "candidate", "evidence": ""},
    {"name": "context7", "source": "Claude Code plugin marketplace",
     "symptom": "the agent writes library calls that are out of date",
     "gives": "current documentation for the library versions in use",
     "cost": "queries go to a hosted service (no account)", "remove": "uninstall the plugin", "status": "candidate", "evidence": ""},
    {"name": "semgrep", "source": "Claude Code plugin marketplace",
     "symptom": "code that handles money or other people's data",
     "gives": "local scanning for known security mistakes as code is written",
     "cost": "a local scanner", "remove": "uninstall the plugin", "status": "candidate", "evidence": ""},
]


def path():
    return board.home() / "supports.json"


def load():
    if not path().exists():
        rows = [dict(r, id=f"s{i + 1}", at=board.now(), projects=[]) for i, r in enumerate(SEED)]
        save(rows)
    return json.loads(path().read_text())


def save(rows):
    board.home().mkdir(parents=True, exist_ok=True)
    path().write_text(json.dumps(rows, indent=1))


def add(name, symptom, gives, source="", cost="", remove="", evidence="", kind="tool"):
    rows = load()
    row = {"id": f"s{max([int(r['id'][1:]) for r in rows] + [0]) + 1}", "at": board.now(), "name": name.strip(),
           "source": source, "symptom": symptom, "gives": gives, "cost": cost, "remove": remove,
           "status": "candidate", "evidence": evidence, "projects": [], "kind": kind}
    save(rows + [row])
    return row


def update(sid, status=None, evidence=None, project=None):
    """Move a support along (with what showed it), or record the project it was installed in."""
    rows = load()
    row = next((r for r in rows if r["id"] == sid), None)
    if row is None:
        raise KeyError(sid)
    if status:
        if status not in STATUSES:
            raise ValueError(status)
        row["status"] = status
    if evidence:
        row["evidence"] = (row["evidence"] + " | " if row["evidence"] else "") + f"{board.now()[:10]}: {evidence.strip()}"
    if project and str(project) not in row["projects"]:
        row["projects"].append(str(project))
    save(rows)
    return row


def suggest(sid, root, text):
    """Offer a proven support to a project's agent as a suggestion it checks against its own work. Only a
    proven one: the agent is asked to try another way only on evidence, never on a hunch."""
    row = next((r for r in load() if r["id"] == sid), None)
    if row is None:
        raise KeyError(sid)
    if row.get("kind") == "reference":
        if not row["evidence"] or row["status"] == "rejected":
            raise ValueError(f"{sid}: a reference is suggested only once it has been read and its evidence says where the better way is")
    elif row["status"] != "proven":
        raise ValueError(f"{sid} is {row['status']}; only a proven tool is suggested to a project")
    lead = "A reference worth a look" if row.get("kind") == "reference" else "A support"
    tail = " Adopt only what works better in your project." if row.get("kind") == "reference" else ""
    board.add_note(root, None, f"{lead}: {row['name']} ({row['source']}): {text.strip()} Shown by: {row['evidence']}.{tail}",
                   author="suggestion", quiet=True)
    update(sid, evidence=f"suggested to {root.name}")
    return row


def text():
    out = []
    for r in load():
        out.append(f"{r['id']} [{r['status']}]{' reference:' if r.get('kind') == 'reference' else ''} {r['name']} ({r['source']})\n"
                   f"    for: {r['symptom']}\n    gives: {r['gives']}\n    cost: {r['cost']}; remove: {r['remove']}"
                   + (f"\n    evidence: {r['evidence']}" if r["evidence"] else "")
                   + (f"\n    in: {', '.join(r['projects'])}" if r["projects"] else ""))
    return "\n".join(out)


def check_now():
    """Make the watcher's next pass run a supports check over the last period, as if it had come due."""
    import time
    hours = board.registry()["settings"]["scout"] or 24
    board.home().mkdir(parents=True, exist_ok=True)
    (board.home() / "scout.json").write_text(json.dumps({"at": time.time() - hours * 3600}))
    return f"a supports check runs within seconds, over projects worked on in the last {hours} hours, once the monitor is idle"
