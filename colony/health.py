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
            escalate(project, row, dup, {"map_in_brief": True},
                     "the project re-made something it already had: builders now see the map of what exists")
    reg = regressions(project, row)
    if reg:
        lost.append(("regression", reg))
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
