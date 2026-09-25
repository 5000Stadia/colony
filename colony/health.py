"""Health: evidence that the project has outgrown its current shape, and the remedy each kind calls for.

Dormant machinery switches itself on at the first real evidence it is needed, and small projects never
pay for it. Memory remedies are cheap and reversible, so they turn on automatically, recorded with their
evidence. Reorganisation is heavier, so strain produces a proposal for the person, with its numbers.
"""
import statistics

# Names too common to mean that something was made twice.


def record_check(project, row, wave, command, passed):
    project.append("ledger.jsonl", {"kind": "check", "row": row, "wave": wave, "command": command, "passed": passed})


def regressions(project, row):
    """Checks that passed when an earlier row closed and fail during this one: the builder broke
    something it did not know it had to keep."""
    ledger = [e for e in project.read("ledger.jsonl") if e["kind"] == "check"]
    passed_before = {e["command"] for e in ledger if e["row"] < row and e["passed"]}
    return sorted({e["command"] for e in ledger if e["row"] == row and not e["passed"] and e["command"] in passed_before})


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


def review_health(project, row):
    """Record what this row shows: checks that had passed and now fail, and strain on one builder.
    Nothing switches itself on. Name-sharing and rising reading fired on most healthy projects
    (garden/harness/replay_*.py), and memory switched on after breaks cost 1.7-2x without helping
    (garden/results/SEEDED.md); so the evidence goes to the checkpoint, and the person decides."""
    lost = []
    reg = regressions(project, row)
    if reg:
        lost.append(("regression", reg))
        project.append("ledger.jsonl", {"kind": "evidence", "row": row, "type": "regression", "detail": reg})
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
    "rows-costlier": "Rows went from about ${first:.2f} to about ${last:.2f} each. Accept it as the project grows, or make rows smaller?",
    "breaks-recur": "Checks that had passed broke again in rows {rows}. Strengthen the checks, switch on NOW and "
                    "reconciliation (in testing about 2x per row, with no gain up to 17 rows), or leave it?",
    "memory-cost": "Keeping memory cost ${keep:.2f} this stretch, {pct}% on top of building. Keep it, or turn it off?",
    "proposed-rows": "Builders proposed {n} row(s) under '## Proposed rows' in the spine: {rows}. Which join the plan?",
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
        lines.append(f"- row {e['row']} broke a check that had passed: {', '.join(e['detail'])[:160]}")
    signs = strain(project)
    for s in signs:
        lines.append(f"- strain: {s}")
    review_cost = sum(u.get("cost_usd", 0) for u in usage if u["agent"].split("@")[0] not in ("builder", "reconciler", "door"))
    build_cost = sum(u.get("cost_usd", 0) for u in usage if u["agent"] == "builder")
    keep_cost = sum(u.get("cost_usd", 0) for u in usage if u["agent"] == "reconciler")
    review_fixes = sum(e.get("review_fixes", 0) for e in closed)
    serious = sum(e.get("serious_fixes", 0) for e in closed)
    lines += ["", "## Tokens"]
    lines.append(f"- ${build_cost + review_cost + keep_cost:.2f} in all: building ${build_cost:.2f}, review ${review_cost:.2f}, "
                 f"keeping memory ${keep_cost:.2f}")
    if len(costs) >= 2:
        lines.append(f"- per row: ${costs[0]:.2f} at the start of the stretch, ${costs[-1]:.2f} at the end, "
                     f"median ${statistics.median(costs):.2f}")
    reviewed = [e for e in reviews if e["review"]]
    if reviewed:
        lines.append(f"- {len(reviewed)} of {len(reviews)} row(s) reviewed; the builder fixed {review_fixes} of review's "
                     f"findings, {serious} of them serious (critical, or found by two reviewers)"
                     + (f", ${review_cost / serious:.2f} per serious fix" if serious else ""))
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
    # Medians of the first and last three rows: single rows vary 2-3x on healthy projects.
    if len(costs) >= 6 and statistics.median(costs[-3:]) > 2 * statistics.median(costs[:3]):
        asks.append(("rows-costlier", dict(first=statistics.median(costs[:3]), last=statistics.median(costs[-3:]))))
    if review_cost > build_cost > 0:
        asks.append(("review-spend", dict(review=review_cost, build=build_cost, fixes=review_fixes)))
    broken = sorted({e["row"] for e in ev if e["type"] == "regression"})
    if len(broken) >= 2:
        asks.append(("breaks-recur", dict(rows=", ".join(map(str, broken)))))
    cfg = project.config()
    if (cfg.get("reconcile") or cfg.get("map_in_brief")) and build_cost and keep_cost > 0.25 * build_cost:
        asks.append(("memory-cost", dict(keep=keep_cost, pct=round(100 * keep_cost / build_cost))))
    waiting = memory.proposed(project)
    if waiting:
        asks.append(("proposed-rows", dict(n=len(waiting), rows="; ".join(f"{n}: {t}" for n, t, _ in waiting)[:300])))
    rules = memory.rules(project)
    questions = [{"kind": k, "ask": QUESTIONS[k].format(**f)} for k, f in asks if k not in rules]
    handled = [f"`{k}` — {rules[k]}" for k, _ in asks if k in rules]
    if handled:
        lines += ["", "## Settled by your rules"] + [f"- {h}" for h in handled]
    lines += ["", "## Questions for you"]
    lines += [f"- `{q['kind']}` — {q['ask']}" for q in questions] or ["- none: nothing here is a tradeoff the records cannot settle"]
    return "\n".join(lines), last, questions
