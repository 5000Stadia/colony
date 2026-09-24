"""Health: evidence that the project has outgrown its current shape, and the remedy each kind calls for.

Dormant machinery switches itself on at the first real evidence it is needed, and small projects never
pay for it. Memory remedies are cheap and reversible, so they turn on automatically, recorded with their
evidence. Reorganisation is heavier, so strain produces a proposal for the person, with its numbers.
"""
import re
import statistics

# Names too common to mean that something was made twice.
COMMON = {"main", "run", "init", "setup", "parse", "load", "save", "get", "set", "update", "build", "check",
          "helper", "format", "render", "validate", "test", "handle", "process", "execute", "call", "apply"}
CODE_KINDS = {"def", "class", "function", "fn", "func", "struct", "enum", "interface", "type"}


def record_check(project, row, wave, command, passed):
    project.append("ledger.jsonl", {"kind": "check", "row": row, "wave": wave, "command": command, "passed": passed})


def regressions(project, row):
    """Checks that passed when an earlier row closed and fail during this one: the builder broke
    something it did not know it had to keep."""
    ledger = [e for e in project.read("ledger.jsonl") if e["kind"] == "check"]
    passed_before = {e["command"] for e in ledger if e["row"] < row and e["passed"]}
    return sorted({e["command"] for e in ledger if e["row"] == row and not e["passed"] and e["command"] in passed_before})


def _symbols(entries):
    out = {}
    for path, item in entries.items():
        for e in item["entries"]:
            if e["kind"] in CODE_KINDS and not e["name"].startswith("test"):
                name = re.sub(r"[^a-z0-9]", "", e["name"].split(".")[-1].lower())
                if len(name) >= 4 and name not in COMMON:
                    out.setdefault(name, set()).add(path)
    return out


def duplicates(before, after):
    """Things this row made that already existed elsewhere under the same name."""
    old, new = _symbols(before), _symbols(after)
    found = []
    for name, paths in new.items():
        added = paths - old.get(name, set())
        elsewhere = old.get(name, set()) - added
        if added and elsewhere:
            found.append(f"{name}: new in {', '.join(sorted(added))}, already in {', '.join(sorted(elsewhere))}")
    return found


def builder_reading(project):
    """Tokens the builder read to do each row's build, by row."""
    per = {}
    for u in project.read("usage.jsonl"):
        if u["agent"] == "builder" and u.get("wave") == 0:
            per[u["row"]] = per.get(u["row"], 0) + u.get("cache_read", 0) + u.get("input", 0)
    return [per[k] for k in sorted(per)]


def rising_reading(project, ratio=1.5):
    """The builder's reading has kept climbing: the last three rows each read well above the first three."""
    r = builder_reading(project)
    if len(r) < 6:
        return None
    base = statistics.median(r[:3]) or 1
    if all(x > ratio * base for x in r[-3:]):
        return f"the builder read {int(statistics.mean(r[-3:]) / base * 100)}% of what it read in the first rows, three rows running"
    return None


def strain(project):
    """A gauge of whether one builder is carrying more than it should: rows far above the typical cost,
    builds that ran out of budget, and files reworked row after row."""
    usage = project.read("usage.jsonl")
    cost = {}
    for u in usage:
        cost[u["row"]] = cost.get(u["row"], 0) + u.get("cost_usd", 0)
    rows = [cost[k] for k in sorted(k for k in cost if k)]
    signs = []
    if len(rows) >= 4:
        typical = statistics.median(rows)
        heavy = [c for c in rows[-4:] if c > 2.5 * typical]
        if len(heavy) >= 2:
            signs.append(f"{len(heavy)} of the last 4 rows cost over 2.5x the typical row (${typical:.2f})")
    capped = [u for u in usage if u["agent"] == "builder" and "budget" in (u.get("subtype") or "")]
    if len(capped) >= 2:
        signs.append(f"the builder ran out of its row budget {len(capped)} times")
    touched = [set(e.get("files", [])) for e in project.read("ledger.jsonl") if e["kind"] == "row-closed"][-4:]
    if len(touched) == 4:
        hot = set.intersection(*touched) - {"design/now.md", "README.md"}
        if hot:
            signs.append(f"{', '.join(sorted(hot))} changed in each of the last four rows")
    return signs


def escalate(project, row, evidence, remedy, why):
    """Switch a memory remedy on, once, with its evidence in the ledger; the person can switch it off."""
    import json
    path = project.state / "config.json"
    cfg = json.loads(path.read_text()) if path.exists() else {}
    changed = {k: v for k, v in remedy.items() if cfg.get(k) != v}
    if not changed:
        return False
    cfg.update(changed)
    path.write_text(json.dumps(cfg, indent=2))
    project.append("ledger.jsonl", {"kind": "escalation", "row": row, "evidence": evidence, "switched_on": changed, "why": why})
    return True


def review_health(project, row, before=None, after=None):
    """Run every detector for this row and act on what it finds."""
    lost = []
    if before is not None and after is not None:
        dup = duplicates(before, after)
        if dup:
            lost.append(("duplicate", dup))
            # A shared name is a heuristic, so one coincidence is only recorded; the second time is evidence.
            project.append("ledger.jsonl", {"kind": "evidence", "row": row, "type": "duplicate", "detail": dup})
            seen = [e for e in project.read("ledger.jsonl") if e["kind"] == "evidence" and e["type"] == "duplicate"]
            if len(seen) >= 2:
                escalate(project, row, [d for e in seen for d in e["detail"]], {"map_in_brief": True},
                         "the project re-made things it already had, twice: builders now see the map of what exists")
    reg = regressions(project, row)
    if reg:
        lost.append(("regression", reg))
        project.append("ledger.jsonl", {"kind": "evidence", "row": row, "type": "regression", "detail": reg})
        escalate(project, row, [f"{c} passed at the last close and failed in row {row}" for c in reg],
                 {"reconcile": True},
                 "the project broke something it had built: NOW and the reasons behind each row now travel forward")
    reading = rising_reading(project)
    if reading:
        escalate(project, row, [reading], {"map_in_brief": True},
                 "re-reading the project is getting expensive: builders now start from the map")
    signs = strain(project)
    if len(signs) >= 2 and not any(e["kind"] == "structure-proposal" and e["row"] >= row - 3 for e in project.read("ledger.jsonl")):
        project.append("ledger.jsonl", {"kind": "structure-proposal", "row": row, "signs": signs,
                                        "proposal": "one builder is carrying more than it should: consider splitting the "
                                                    "most-reworked area into its own lane"})
    return lost, signs


def since_checkpoint(project):
    """Every ledger and usage record since the last checkpoint, and the row it was taken at."""
    ledger = project.read("ledger.jsonl")
    marks = [i for i, e in enumerate(ledger) if e["kind"] == "checkpoint"]
    start_row = ledger[marks[-1]]["row"] if marks else 0
    return [e for e in ledger[(marks[-1] + 1 if marks else 0):]], start_row


def overview(project):
    """A broad, free look at effectiveness since the last checkpoint: progress, cost, what review returned,
    lost context, strain — and the patterns that cost without returning anything."""
    ledger, start = since_checkpoint(project)
    closed = [e for e in ledger if e["kind"] == "row-closed"]
    usage = [u for u in project.read("usage.jsonl") if u["row"] > start]
    reviews = [e for e in ledger if e["kind"] == "review"]
    reviewed_rows = {e["row"] for e in reviews if e["review"]}
    review_cost = sum(u.get("cost_usd", 0) for u in usage if u["agent"].split("@")[0] not in ("builder", "reconciler", "door"))
    build_cost = sum(u.get("cost_usd", 0) for u in usage if u["agent"] == "builder")
    fixes = sum(e.get("review_fixes", 0) for e in closed)
    costs = [e["cost_usd"] for e in closed]
    if not closed:
        return f"# Checkpoint — no rows closed since row {start}", start
    lines = [f"# Checkpoint — rows {start + 1}–{closed[-1]['row']}", ""]
    lines.append(f"- **Progress:** {len(closed)} row(s) closed for ${sum(costs):.2f}" + (
        f"; per row ${costs[0]:.2f} at the start of the stretch, ${costs[-1]:.2f} at the end" if len(costs) >= 2 else ""))
    lines.append(f"- **Review:** {len(reviewed_rows)} of {len(reviews)} row(s) reviewed, ${review_cost:.2f} "
                 f"(building ${build_cost:.2f}); review fixed {fixes} real problem(s)"
                 + (f" — ${review_cost / fixes:.2f} per fix" if fixes else ""))
    moves = [e for e in ledger if e["kind"] == "threshold"]
    if moves:
        lines.append("- **Review threshold:** " + "; ".join(f"{m['from']}→{m['to']} ({m['why']})" for m in moves))
    ev = [e for e in ledger if e["kind"] == "evidence"]
    if ev:
        lines.append("- **Evidence of lost context:** " + "; ".join(f"row {e['row']} {e['type']}: {', '.join(e['detail'])[:160]}" for e in ev))
    esc = [e for e in ledger if e["kind"] == "escalation"]
    if esc:
        lines.append("- **Switched on:** " + "; ".join(f"after row {e['row']}: {', '.join(e['switched_on'])}" for e in esc))
    signs = strain(project)
    if signs:
        lines.append("- **Strain on one builder:** " + "; ".join(signs))
    for p in (e for e in ledger if e["kind"] == "structure-proposal"):
        lines.append(f"- **Proposed after row {p['row']}:** {p.get('proposal', '')}")
    stops = [e for e in ledger if e["kind"] == "run-stopped"]
    if stops:
        lines.append(f"- **Runs stopped:** {len(stops)} — last: {stops[-1].get('reason', '')[:160]}")
    # Quality first: what went wrong comes before what cost money, and review of the person's risky areas
    # is never offered up for saving — a quiet review there is the insurance working.
    look = [f"row {e['row']} broke a check that had passed" for e in ev if e["type"] == "regression"]
    look += [f"row {e['row']} re-made something the project already had" for e in ev if e["type"] == "duplicate"]
    if len(ev) >= 2 and not esc:
        look.append("lost context keeps showing without a remedy switched on")
    unforced = [e["row"] for e in reviews if e["review"] and not e["why"].startswith("touches risky areas")]
    unforced_fixes = sum(e.get("review_fixes", 0) for e in closed if e["row"] in unforced)
    if len(unforced) >= 3 and not unforced_fixes:
        look.append(f"{len(unforced)} reviews outside the risky areas found nothing; the threshold is already easing")
    if len(costs) >= 4 and costs[-1] > 2 * costs[0]:
        look.append("rows are getting more expensive as the project grows")
    lines.append("- **Worth a look:** " + ("; ".join(look) if look else "nothing stands out"))
    return "\n".join(lines), (closed[-1]["row"] if closed else start)
