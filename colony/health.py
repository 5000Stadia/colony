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


# The tradeoffs a checkpoint asks the person about, because no count settles them. A rule in the
# spine's `## Rules` (``- `kind` — what to do``) answers one in advance.
QUESTIONS = {
    "row-stuck": "Row {row} has stopped {n} times (last: {reason}). Keep going, rescope it, or split it?",
    "lane": "One builder is carrying a lot: {signs}. Give that area its own lane, or keep one builder?",
    "rows-costlier": "Rows went from ${first:.2f} to ${last:.2f} each. Accept it as the project grows, or make rows smaller?",
    "review-spend": "Review cost ${review:.2f} against ${build:.2f} for building, and fixed {fixes}. Keep it as it is, "
                    "or narrow the risky areas?",
}


def overview(project):
    """A broad look since the last checkpoint, computed from the records: is the work moving, is the
    workflow effective, are tokens well spent — and the tradeoffs only the person can settle."""
    from . import memory
    ledger, start = since_checkpoint(project)
    closed = [e for e in ledger if e["kind"] == "row-closed"]
    usage = [u for u in project.read("usage.jsonl") if u["row"] > start]
    events = [e for e in project.read("field.jsonl") if e.get("row", 0) > start]
    reviews = [e for e in ledger if e["kind"] == "review"]
    ev = [e for e in ledger if e["kind"] == "evidence"]
    esc = [e for e in ledger if e["kind"] == "escalation"]
    stops = [e for e in ledger if e["kind"] == "run-stopped"]
    costs = [e["cost_usd"] for e in closed]
    last = closed[-1]["row"] if closed else start
    lines = [f"# Checkpoint — since row {start}", "", "## Workflow and progress"]
    lines.append(f"- {len(closed)} row(s) closed" + (f" (rows {closed[0]['row']}–{last})" if closed else "")
                 + (f"; {len(stops)} run(s) stopped" if stops else ""))
    stuck = {}
    for s in stops:
        stuck.setdefault(s.get("row"), []).append(s)
    for row, ss in stuck.items():
        lines.append(f"- row {row} stopped {len(ss)} time(s); last: {ss[-1].get('reason', '')[:160]}")
    rounds = {u["row"] for u in usage if u["agent"] == "builder" and u["wave"] >= 1}
    fixed = sum(1 for e in events if e["type"] == "resolve" and e.get("fixed"))
    declined = sum(1 for e in events if e["type"] == "resolve" and not e.get("fixed"))
    unfixed = sum(1 for e in events if e["type"] == "signal" and e["kind"] == "unfixed")
    lines.append(f"- review sent {len(rounds)} row(s) back for fixes: {fixed} fixed, {declined} declined"
                 + (f", {unfixed} fix(es) that did not hold" if unfixed else ""))
    for e in ev:
        what = "broke a check that had passed" if e["type"] == "regression" else "re-made something the project already had"
        lines.append(f"- row {e['row']} {what}: {', '.join(e['detail'])[:160]}")
    for e in esc:
        lines.append(f"- switched on after row {e['row']}: {', '.join(e['switched_on'])}")
    signs = strain(project)
    for s in signs:
        lines.append(f"- strain: {s}")
    review_cost = sum(u.get("cost_usd", 0) for u in usage if u["agent"].split("@")[0] not in ("builder", "reconciler", "door"))
    build_cost = sum(u.get("cost_usd", 0) for u in usage if u["agent"] == "builder")
    keep_cost = sum(u.get("cost_usd", 0) for u in usage if u["agent"] == "reconciler")
    review_fixes = sum(e.get("review_fixes", 0) for e in closed)
    lines += ["", "## Tokens"]
    lines.append(f"- ${build_cost + review_cost + keep_cost:.2f} in all: building ${build_cost:.2f}, review ${review_cost:.2f}, "
                 f"keeping memory ${keep_cost:.2f}")
    if len(costs) >= 2:
        lines.append(f"- per row: ${costs[0]:.2f} at the start of the stretch, ${costs[-1]:.2f} at the end, "
                     f"median ${statistics.median(costs):.2f}")
    reviewed = [e for e in reviews if e["review"]]
    if reviewed:
        lines.append(f"- {len(reviewed)} of {len(reviews)} row(s) reviewed; review fixed {review_fixes} real problem(s)"
                     + (f", ${review_cost / review_fixes:.2f} per fix" if review_fixes else ""))
    reading = rising_reading(project)
    if reading:
        lines.append(f"- {reading}")
    for m in (e for e in ledger if e["kind"] == "threshold"):
        lines.append(f"- review threshold {m['from']}→{m['to']}: {m['why']}")
    asks = []
    for row, ss in stuck.items():
        if len(ss) >= 2:
            asks.append(("row-stuck", dict(row=row, n=len(ss), reason=ss[-1].get("reason", "")[:120])))
    if len(signs) >= 2:
        asks.append(("lane", dict(signs="; ".join(signs))))
    if len(costs) >= 4 and costs[-1] > 2 * costs[0]:
        asks.append(("rows-costlier", dict(first=costs[0], last=costs[-1])))
    if review_cost > build_cost > 0:
        asks.append(("review-spend", dict(review=review_cost, build=build_cost, fixes=review_fixes)))
    rules = memory.rules(project)
    questions = [{"kind": k, "ask": QUESTIONS[k].format(**f)} for k, f in asks if k not in rules]
    handled = [f"`{k}` — {rules[k]}" for k, _ in asks if k in rules]
    if handled:
        lines += ["", "## Settled by your rules"] + [f"- {h}" for h in handled]
    lines += ["", "## Questions for you"]
    lines += [f"- `{q['kind']}` — {q['ask']}" for q in questions] or ["- none: nothing here is a tradeoff the records cannot settle"]
    return "\n".join(lines), last, questions
